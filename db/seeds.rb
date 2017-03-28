require 'csv'

states = [
            ['Georgia', 'GA'],
            ['North Carolina', 'NC'],
            ['Tennessee', 'TN'],
            ['Virginia', 'VA'],
            ['Maryland', 'MD'],
            ['Pennsylvania', 'PA'],
            ['New Jersey', 'NJ'],
            ['New York', 'NY'],
            ['Connecticut', 'CT'],
            ['Massachusetts', 'MA'],
            ['Vermont', 'VT'],
            ['New Hampshire', 'NH'],
            ['Maine', 'ME']
          ]
states.each do |state|
  State.create(name: state.first, abbreviation: state.last)
end

shelters_csv = File.read(Rails.root.join('lib', 'seeds', 'shelters.csv'))
csv = CSV.parse(shelters_csv, headers: true)
csv.each do |row|
  state = State.where(abbreviation: row['State']).first
  Shelter.create(name: row['Name'], mileage: row['Mileage'],
    elevation: row['Elevation'], long: row['Long'], latt: row['Latt'],
    state: state)
end
AdminUser.create!(email: 'admin@example.com', password: 'password', password_confirmation: 'password')