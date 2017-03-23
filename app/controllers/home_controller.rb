class HomeController < ApplicationController

  def index
    cached_html = File.new(Rails.root.join('app', 'views', 'static', 'index.html'), 'r')
    seconds_in_hour = 3600.0
    if ((Time.now - cached_html.mtime).to_i / seconds_in_hour > 2)

      @states = {}
      states = State.all

      states.each do |state|
        state_info = {}
        state_info[:average_weather] = state.get_average_weather
        state_info[:shelters] = Shelter.where(state: state)
        @states[:"#{state.name}"] = state_info
      end
      rendered_html = render_to_string(template: 'home/index')
      compressor = HtmlCompressor::Compressor.new
      File.write(Rails.root.join('app', 'views', 'static', 'index.html'), compressor.compress(rendered_html))
    end

    render html: cached_html.read.html_safe
  end

  def nearest_shelter
    @shelter = Shelter.order("ABS(mileage - #{ params[:mileage]})").first
    @weather_days = Weatherable.show_shelter(@shelter)
    render 'shelters/show'
  end


end
