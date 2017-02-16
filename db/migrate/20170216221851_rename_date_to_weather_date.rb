class RenameDateToWeatherDate < ActiveRecord::Migration[5.0]
  def change
    rename_column :weathers, :date, :weather_date
  end
end
