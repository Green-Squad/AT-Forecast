class HomeController < ApplicationController

  def index
    respond_to do |format|
      format.html {
        cached_html_path = Rails.root.join('public', 'cached_pages', 'index.html')
        cached_html = Pathname(cached_html_path).exist? ? File.new(cached_html_path, 'r') : nil
        seconds_in_hour = 3600.0
        if (cached_html.nil? || (Time.now - cached_html.mtime).to_i / seconds_in_hour > 2)
          @states = Weatherable.get_states
          rendered_html = render_to_string(template: 'home/index')
          compressor = HtmlCompressor::Compressor.new
          File.write(cached_html_path, compressor.compress(rendered_html))
          cached_html = File.new(cached_html_path, 'r')
        end

        render html: cached_html.read.html_safe
      }
      format.json {
        @include_shelters = params[:include_shelters] == 'true' ? true : false
        @states = Weatherable.get_states
        render template: 'home/index.json'
      }
    end
  end

  def nearest_shelter
    if params[:nobo_mile].present?
      @shelter = Shelter.order("ABS(mileage - #{params[:nobo_mile]})").first
      @weather_days = Weatherable.show_shelter(@shelter)
      render 'shelters/show'
    elsif params[:coords].present?
      coords = params[:coords].split(',')
      @shelter = Shelter.find_by_nearest_coords(coords.first, coords.last)
      @weather_days = Weatherable.show_shelter(@shelter)
      render 'shelters/show'
    else
      redirect_to root_url
    end

  end


end
