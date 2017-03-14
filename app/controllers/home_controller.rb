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
    @weather = Weather.where(shelter: @shelter)
    render 'shelters/show'
  end


end
