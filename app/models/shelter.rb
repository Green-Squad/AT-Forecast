class Shelter < ApplicationRecord
  require 'haversine'

  has_many :weathers
  belongs_to :state

  attr_accessor :daily_weather

  def next
    self.class.where("mileage > ?", mileage).first
  end

  def previous
    self.class.where("mileage < ?", mileage).last
  end

  def self.find_by_nearest_coords(latt, long)
    smallest_distance = 9999999999999.0
    smallest_shelter = nil
    Shelter.all.each do |shelter|
      distance = ::Haversine.spherical_distance([latt.to_f, long.to_f], [shelter.latt.to_f, shelter.long.to_f])
      if (distance < smallest_distance)
        smallest_distance = distance
        smallest_shelter = shelter
      end
    end
    smallest_shelter
  end

  def self.update_all
    logger.debug("Running Shelter.update_all at #{Time.now}")
    Shelter.all.each do |shelter|
      logger.debug("Start shelter.update_weather for #{shelter.id}")
      shelter.update_weather
      logger.debug("End shelter.update_weather for #{shelter.id}")
      logger.debug("Start shelter.update_hourly_weather for #{shelter.id}")
      shelter.update_hourly_weather
      logger.debug("End shelter.update_hourly_weather for #{shelter.id}")
      shelter_cache_path = "#{Rails.root}/public/cached_pages/shelters/#{shelter.id}.html"
      if File.exists?(shelter_cache_path)
        logger.debug("File exists at #{shelter_cache_path}")
        begin
          File.delete(shelter_cache_path)
        rescue Exception => e
          logger.debug ("Error. Could not delete #{shelter_cache_path}")
          logger.debug e.message
          logger.debug e.backtrace.inspect
        end
      end
      sleep 2
    end
    index_cache_path = "#{Rails.root}/public/cached_pages/index.html"
    File.delete(index_cache_path) if File.exists?(index_cache_path)
  end

  def load_weather(type)
    base_url = 'http://api.openweathermap.org/data/2.5/forecast'
    if type == 'daily'
      base_url += '/daily?'
    elsif type == 'hourly'
      base_url += '?'
    end
    api_key = '&appid=' + ENV['OPEN_WEATHER_MAP_API_KEY']
    request_url = base_url + "lat=#{self.latt}&lon=#{self.long}" + api_key
    begin
      request = open(request_url)
      JSON.load(request)
    rescue Exception => e
      logger.debug ("Error. Could not load request #{request_url}")
      logger.debug e.message
      logger.debug e.backtrace.inspect
      nil
    end
  end

  def convert_wind(speed_in_ms, degrees)
    speed_in_mph = (speed_in_ms / 0.44704).round;
    direction_index = ((degrees / 45) + 0.5).to_i % 8
    directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    "#{speed_in_mph} mph #{directions[direction_index]}"
  end

  def convert_temp(kelvin)
     kelvin * 9.0/5.0 - 459.67
  end

  def update_weather
    forecast = load_weather('daily')
    unless forecast.nil?
      latt = forecast['city']['coord']['lat']
      long = forecast['city']['coord']['lon']
      elevation = get_elevation(latt, long)
      weather_array = []
      forecast['list'].each do |weather|
        weather_date = DateTime.strptime("#{weather['dt']}",'%s')
        high_f = convert_temp(weather['temp']['max'])
        high = adjust_temp_for_elevation(high_f, elevation)
        low_f = convert_temp(weather['temp']['min'])
        low = adjust_temp_for_elevation(low_f, elevation)
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
  end

  def update_hourly_weather
    forecast = load_weather('hourly')
    unless forecast.nil?
      latt = forecast['city']['coord']['lat']
      long = forecast['city']['coord']['lon']
      logger.debug(latt.inspect + " / " + latt.class.name)
      elevation = get_elevation(latt, long)
      hourly_weather_array = []
      forecast['list'].each do |weather|
        date = DateTime.strptime("#{weather['dt']}",'%s')
        temp_f = convert_temp(weather['main']['temp'])
        temp = adjust_temp_for_elevation(temp_f, elevation)
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
  end

  def get_elevation(latt, long)
    elevation = Elevation.where(latt: latt.to_d, long: long.to_d).first
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
