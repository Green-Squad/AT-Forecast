class State < ApplicationRecord
  has_many :shelters
  
  def get_average_weather
    date = Time.now
    state_id = self.id
    weathers = Weather.joins("INNER JOIN shelters
      ON shelters.id = weathers.shelter_id WHERE shelters.state_id = #{state_id}
      AND weathers.weather_date <= '#{date}'").select('weathers.id, high, low')
    low = 0
    high = 0
    weathers.each do |weather|
      low += weather.low
      high += weather.high
    end

    low /= weathers.size if weathers.size > 0
    high /= weathers.size if weathers.size > 0

    {low: low, high: high}

  end
end
