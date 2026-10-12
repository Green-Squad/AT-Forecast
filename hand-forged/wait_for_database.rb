require 'pg'
require 'json'
expected = JSON.parse(File.read('hand-forged/seed-manifest.json'))
ready = false
60.times do
  begin
    c = PG.connect(host: '127.0.0.1', dbname: 'fast_at_weather_production', user: 'handforged', password: ENV.fetch('FASTATWEATHER_DATABASE_PASSWORD'))
    ready = c.exec('SELECT COUNT(*) FROM shelters').getvalue(0,0).to_i == expected.fetch('shelters') && c.exec('SELECT COUNT(*) FROM weathers').getvalue(0,0).to_i == expected.fetch('daily_forecasts') && c.exec('SELECT COUNT(*) FROM hourly_weathers').getvalue(0,0).to_i == expected.fetch('hourly_forecasts')
    c.close
    break if ready
  rescue PG::Error
  end
  sleep 1
end
abort 'Disposable database startup/seeding failed' unless ready
puts 'HAND_FORGED_DATABASE_READY: populated local PostgreSQL'
