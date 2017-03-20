class AddWindToHourlyWeathers < ActiveRecord::Migration[5.0]
  def change
    add_column :hourly_weathers, :wind, :string
  end
end
