class RemovePrecipChanceFromHourlyWeathers < ActiveRecord::Migration[5.0]
  def change
    remove_column :hourly_weathers, :precip_chance, :integer
  end
end
