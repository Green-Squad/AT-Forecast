class Shelter < ApplicationRecord
  has_many :weathers
  belongs_to :state

  def next
    self.class.where("mileage > ?", mileage).first
  end

  def previous
    self.class.where("mileage < ?", mileage).last
  end

  def self.update_all
    Thread.new do
      Shelter.all.each do |shelter|
        shelter.update_weather
        shelter.update_hourly_weather
        shelter_cache_path = "#{Rails.root}/public/cached_pages/shelters/#{shelter.id}.html"
        File.delete(shelter_cache_path) if File.exists?(shelter_cache_path)
        sleep 2
      end
      index_cache_path = "#{Rails.root}/public/cached_pages/index.html"
      File.delete(index_cache_path) if File.exists?(index_cache_path)
    end
  end

  def load_weather(type)
    base_url = 'http://api.openweathermap.org/data/2.5/forecast'
    if type == 'daily'
      base_url += '/daily?'
    elsif type == 'hourly'
      base_url += '?'
    end
    api_key = '&appid=' + ENV['OPEN_WEATHER_MAP_API_KEY']
    units = '&units=imperial'
    request_url = base_url + "lat=#{self.latt}&lon=#{self.long}" + units + api_key
    JSON.load(open(request_url))
  end

  def convert_wind(speed_in_ms, degrees)
    speed_in_mph = (speed_in_ms / 0.44704).round;
    direction_index = ((degrees / 45) + 0.5).to_i % 8
    directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    "#{speed_in_mph} mph #{directions[direction_index]}"
  end

  def update_weather
    forecast = load_weather('daily')
    latt = forecast['city']['coord']['lat']
    long = forecast['city']['coord']['lon']
    elevation = get_elevation(latt, long)
    weather_array = []
    forecast['list'].each do |weather|
      weather_date = DateTime.strptime("#{weather['dt']}",'%s')
      high = adjust_temp_for_elevation(weather['temp']['max'], elevation)
      low = adjust_temp_for_elevation(weather['temp']['min'], elevation)
      description = weather['weather'][0]['description']
      wind_direction = weather['deg']
      wind_speed = weather['speed']
      wind = convert_wind(wind_speed, wind_direction)

      weather_array << { weather_date: weather_date, high: high, low: low,
        description: description, wind: wind, shelter:self }
    end
    if weather_array.any?
      Weather.where(shelter: self).delete_all
      Weather.create(weather_array)
    end
  end

  def update_hourly_weather
    forecast = load_weather('hourly')
    latt = forecast['city']['coord']['lat']
    long = forecast['city']['coord']['lon']
    elevation = get_elevation(latt, long)
    hourly_weather_array = []
    forecast['list'].each do |weather|
      date = DateTime.strptime("#{weather['dt']}",'%s')
      temp = adjust_temp_for_elevation(weather['main']['temp'], elevation)
      description = weather['weather'][0]['description']
      wind_direction = weather['wind']['deg']
      wind_speed = weather['wind']['speed']
      wind = convert_wind(wind_speed, wind_direction)

      hourly_weather_array << {date: date, temp: temp,
        description: description, wind: wind, shelter:self}
    end
    if hourly_weather_array.any?
      HourlyWeather.where(shelter: self).delete_all
      HourlyWeather.create(hourly_weather_array)
    end
  end

  def get_elevation(latt, long)
    elevation = Elevation.where(latt: latt, long: long).first
    unless (elevation.present?)
      api_key = ENV['GOOGLE_MAPS_API_KEY']
      request_url = "https://maps.googleapis.com/maps/api/elevation/json?locations=#{latt},#{long}&key=#{api_key}"
      results = JSON.load(open(request_url))
      elevation_result = results['results'].first['elevation'].round
      elevation = Elevation.create(latt: latt, long: long, elevation: elevation_result)
    end
    elevation.elevation
  end

  def adjust_temp_for_elevation(temp, elevation)
    temp_diff_per_1k_feet = 3.5
    elevation_diff = self.elevation - elevation
    temp - (elevation_diff / 1000.0 * temp_diff_per_1k_feet)
  end

end
