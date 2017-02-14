class HomeController < ApplicationController

  def index
    @shelters_states = {}
    @states = State.all
    @states.each do |state|
      @shelters_states[:"#{state.name}"] = Shelter.where(state: state)
    end
  end


end
