json.extract! @shelter, :id, :name, :mileage, :elevation
json.daily_weather @weather_days do |weather_day|

  json.extract! weather_day[:daily], :id, :weather_date, :high, :low, :description, :wind, :shelter_id
  json.hourly_weather weather_day[:hourly] do |hourly|
    json.extract! hourly, :id, :date, :temp, :description, :wind
  end
end
