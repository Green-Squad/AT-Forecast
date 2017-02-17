class Shelter < ApplicationRecord
  belongs_to :state

  def update_weather

    Weather.where(shelter: self).delete_all

    base_url = 'http://api.openweathermap.org/data/2.5/forecast/daily?'
    api_key = '&appid=d6c2dca5173b6aa1e1975757d2eac3e2'
    units = '&units=imperial'
    request_url = base_url + "lat=#{self.latt}&lon=#{self.long}" + units = '&units=imperial' + api_key
    forecast = JSON.load(open(request_url))

    forecast['list'].each do |weather|
    #  weather_date = weather['dt']
      weather_date = DateTime.strptime("#{weather['dt']}",'%s')
      high = weather['temp']['max']
      low = weather ['temp']['min']
      description = weather['weather'][0]['description']
      wind_direction = weather['deg']
      wind_speed = weather['speed']

      description += " Wind: #{wind_speed} m/s at #{wind_direction} degrees"
      Weather.create(weather_date: weather_date, high: high, low: low,
        description: description, precip_chance:0, shelter:self)
#      precip_chance =
    end
  end
end
