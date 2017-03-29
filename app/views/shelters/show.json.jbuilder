json.array! @weather_days do |weather_day|
    counter = 0
    json.array! weather_day do |weather|
      if counter.odd?
        json.array! weather do |hourly|
          json.extract! hourly, :id, :date, :temp, :description, :wind
        end
      else
        json.extract! weather, :id, :weather_date, :high, :low, :description, :wind, :shelter_id
      end
      counter = counter + 1
    end
end
