json.extract! state, :id, :name, :abbreviation, :created_at, :updated_at
json.url state_url(state, format: :json)