class SheltersController < ApplicationController
  before_action :set_shelter, only: [:show]
  caches_page :show

  # GET /shelters/1
  # GET /shelters/1.json
  def show
    @weather_days = Weatherable.show_shelter(@shelter)
  end

  private
    # Use callbacks to share common setup or constraints between actions.
    def set_shelter
      @shelter = Shelter.find(params[:id])
    end

    # Never trust parameters from the scary internet, only allow the white list through.
    def shelter_params
      params.require(:shelter).permit(:name, :mileage, :elevation, :long, :latt, :state_id)
    end
end
