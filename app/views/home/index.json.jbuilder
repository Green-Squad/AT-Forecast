json.array! @states do |state, state_info|
  json.name state
  json.average_high state_info[:average_weather][:high]
  json.average_low state_info[:average_weather][:low]
  json.shelters state_info[:shelters] do |shelter|
    json.shelter_id shelter[:id]
    json.extract! shelter, :name, :mileage
  end
end
