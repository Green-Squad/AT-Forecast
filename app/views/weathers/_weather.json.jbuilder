json.extract! weather, :id, :date, :high, :low, :description, :precip_chance, :shelter_id, :created_at, :updated_at
json.url weather_url(weather, format: :json)