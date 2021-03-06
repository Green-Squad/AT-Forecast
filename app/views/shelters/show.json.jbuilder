json.array! @shelters do |shelter|
  json.shelter_id shelter.id
  json.extract! shelter, :name, :mileage, :elevation
  json.southbound_mileage shelter.get_southbound_mileage
  json.daily_weather shelter.daily_weather do |weather_day|
    json.daily_weather_id weather_day[:daily][:id]
    json.extract! weather_day[:daily], :weather_date, :high, :low, :description, :wind, :shelter_id
    json.hourly_weather weather_day[:hourly] do |hourly|
      json.hourly_weather_id hourly[:id]
      json.extract! hourly, :date, :temp, :description, :wind
    end
  end
end
