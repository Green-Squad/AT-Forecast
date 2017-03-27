class HomeController < ApplicationController

  def index
    cached_html_path = Rails.root.join('public', 'cached_pages', 'index.html')
    cached_html = Pathname(cached_html_path).exist? ? File.new(cached_html_path, 'r') : nil
    seconds_in_hour = 3600.0
    if (cached_html.nil? || (Time.now - cached_html.mtime).to_i / seconds_in_hour > 2)
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
      File.write(cached_html_path, compressor.compress(rendered_html))
      cached_html = File.new(cached_html_path, 'r')
    end

    render html: cached_html.read.html_safe
  end

  def nearest_shelter
    @shelter = Shelter.order("ABS(mileage - #{ params[:mileage]})").first
    @weather_days = Weatherable.show_shelter(@shelter)
    render 'shelters/show'
  end


end
