class HomeController < ApplicationController

  def index
    @states = {}
    states = State.all

    states.each do |state|
      state_info = {}
      state_info[:average_weather] = state.get_average_weather
      state_info[:shelters] = Shelter.where(state: state)
      @states[:"#{state.name}"] = state_info
    end
  end

  def nearest_shelter
    @shelter = Shelter.order("ABS(mileage - #{ params[:mileage]})").first
    @shelter.update_weather
    @shelter.update_hourly_weather
    daily_weather = Weather.where(shelter: @shelter).order(:weather_date)
    @weather_days = []
    daily_weather.each do |dw|
      date = dw.weather_date
      hourly_weather = HourlyWeather.where(shelter: @shelter,
        date: date.beginning_of_day..date.end_of_day)
      all_weather = [dw, hourly_weather]
      @weather_days << all_weather
    end
    render 'shelters/show'
  end


end
