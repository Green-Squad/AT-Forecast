json.array! @states do |state, state_info|
  json.state_id state_info[:id]
  json.name state
  json.average_high state_info[:average_weather][:high]
  json.average_low state_info[:average_weather][:low]
  if @include_shelters
    json.shelters state_info[:shelters] do |shelter|
      json.shelter_id shelter[:id]
      json.extract! shelter, :name, :mileage, :elevation, :latt, :long
      json.southbound_mileage shelter.get_southbound_mileage
    end
  end
end
