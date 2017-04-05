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

end
