> The `rust` branch contains a public Rust reader without query batching. See [Rust implementation, execution, and comparison instructions](rust/README.md). The original Rails web Dockerfile is `Dockerfile.rails`; the weather refresh job remains Rails.

### AT Forecast

We wanted to create a faster and more intuitive website for checking the weather while on the Appalachian Trail. Our two main goals for this project were speed and a good mobile experience. The site was designed for phones first since that is where hikers will be checking it, but it also looks great on desktop. Our pages are about 10 KiB on average and load in around 300 ms. Sections of the trail would tend to have poor mobile reception (depending on your cell service provider), so we wanted the pages to load in as fast as possible.

We welcome any pull requests or suggestions that you might want to see implemented to further improve the app.

Data sources we use:  
- Shelter Data: https://www.whiteblaze.net/forum/shelters-2016.php  
- Weather Data: https://openweathermap.org/api
  
