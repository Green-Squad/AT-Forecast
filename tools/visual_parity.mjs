// Public rendering parity on isolated Rails/Rust fixture servers. Never uses production data.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {chromium} from 'playwright';
const [rails,rust]=process.argv.slice(2);
for(const base of [rails,rust]) assert.ok(['localhost','127.0.0.1','[::1]'].includes(new URL(base).hostname),'Only isolated local servers are permitted');
const output='benchmark-results/visual-parity';await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true});
const reports=[];
try {
  for(const width of [390,768,805,1280]) {
    const context=await browser.newContext({viewport:{width,height:900},deviceScaleFactor:1});
    for(const route of ['/', '/about', '/shelters/2', '/shelters/3', '/shelters/4']) {
      const pages=[];
      for(const base of [rails,rust]) {
        const page=await context.newPage();
        await page.route(/googletagmanager\.com|google-analytics\.com/,r=>r.abort());
        const response=await page.goto(base+route,{waitUntil:'networkidle'});
        assert.equal(response.status(),200,route);
        const stylesheet=await page.locator('link[rel=stylesheet]').getAttribute('href');
        const css=await page.request.get(new URL(stylesheet,page.url()).href);
        assert.equal(css.status(),200,`${base}: comparison stylesheet must be served`);
        assert.ok(await page.evaluate(()=>document.styleSheets.length>0),'Stylesheet must be loaded');
        pages.push(page);
      }
      const states=['normal','hover-about','focus-github',...(route==='/'?['expanded-state']:[])];
      for(const state of states) {
        const captures=[];
        for(let i=0;i<pages.length;i++) {
          const page=pages[i];
          if(state==='hover-about') await page.getByRole('link',{name:'About',exact:true}).hover();
          if(state==='focus-github') await page.getByRole('link',{name:'About',exact:true}).press('Tab');
          if(state==='expanded-state') await page.locator('.state').first().click();
          const snapshot=await page.evaluate(()=>Array.from(document.querySelector('#layout').querySelectorAll('*')).filter(e=>e.tagName!=='SCRIPT').map(e=>{
            const style=getComputedStyle(e),rect=e.getBoundingClientRect();
            return {tag:e.tagName,attrs:Object.fromEntries(Array.from(e.attributes).map(a=>[a.name,a.value.replace(location.origin,'').replace(/^https?:\/\/(?:www\.)?atforecast\.app(?=\/)/,'')]).sort(([a],[b])=>a.localeCompare(b))),text:e.children.length?'':e.textContent.replace(/\s+/g,' ').trim(),rect:[rect.x,rect.y,rect.width,rect.height],styles:Object.fromEntries(Array.from(style).map(k=>[k,style.getPropertyValue(k)]))};
          }));
          const prefix=path.join(output,`${width}-${route.replaceAll('/','_')}-${state}-${i?'rust':'rails'}`);
          await fs.writeFile(prefix+'.json',JSON.stringify(snapshot,null,2));
          const image=await page.screenshot({path:prefix+'.png',fullPage:true});
          captures.push({snapshot,image});
        }
        assert.deepEqual(captures[1].snapshot,captures[0].snapshot,`${width} ${route} ${state}: DOM/computed styles/layout differ`);
        assert.ok(captures[1].image.equals(captures[0].image),`${width} ${route} ${state}: screenshot differs`);
        reports.push({width,route,state,pass:true});console.log('PASS',width,route,state);
      }
      await Promise.all(pages.map(p=>p.close()));
    }
    await context.close();
  }
} finally {await browser.close();await fs.writeFile(path.join(output,'summary.json'),JSON.stringify(reports,null,2));}
