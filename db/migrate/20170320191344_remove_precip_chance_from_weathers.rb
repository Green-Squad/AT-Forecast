class RemovePrecipChanceFromWeathers < ActiveRecord::Migration[5.0]
  def change
    remove_column :weathers, :precip_chance, :integer
  end
end
