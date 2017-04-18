module Weatherable extend ActiveSupport::Concern

  def self.show_shelter(shelter)
    daily_weather = Weather.where(shelter: shelter).order(:weather_date)
    weather_days = []
    daily_weather.each do |dw|
      date = dw.weather_date
      hourly_weather = HourlyWeather.where(shelter: shelter,
        date: date.beginning_of_day..date.end_of_day).order(:date)
      all_weather = {daily: dw, hourly: hourly_weather}
      weather_days << all_weather
    end
    weather_days
  end

  def self.get_states
    states = {}
    states_collection = State.all

    states_collection.each do |state|
      state_info = {}
      state_info[:average_weather] = state.get_average_weather
      state_info[:shelters] = Shelter.where(state: state).order(:mileage)
      state_info[:id] = state.id
      states[:"#{state.name}"] = state_info
    end

    states
  end

end
