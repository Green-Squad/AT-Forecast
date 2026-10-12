# Only the external response changes. Shelter's original conversion and write methods remain intact.
module HandForgedWeather
  def self.for(shelter, type)
    offset = shelter.id.to_i % 15
    day = Time.zone.today
    city = { 'coord' => { 'lat' => shelter.latt.to_f, 'lon' => shelter.long.to_f } }
    descriptions = ['clear sky', 'scattered clouds', 'light rain']
    if type == 'daily'
      list = (0...7).map do |n|
        { 'dt' => (day + n).in_time_zone.change(hour: 12).to_i,
          'temp' => { 'max' => 283.15 + offset + n % 3, 'min' => 273.15 + offset },
          'weather' => [{ 'description' => descriptions[n % 3] }], 'deg' => 225, 'speed' => 2.5 }
      end
    else
      list = (0...56).map do |n|
        { 'dt' => day.in_time_zone.to_i + n * 3 * 3600,
          'main' => { 'temp' => 278.15 + offset + n % 4 },
          'weather' => [{ 'description' => descriptions[n % 3] }], 'wind' => { 'deg' => 225, 'speed' => 2.5 } }
      end
    end
    { 'city' => city, 'list' => list }
  end
end
