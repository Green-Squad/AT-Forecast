class AddWindToWeathers < ActiveRecord::Migration[5.0]
  def change
    add_column :weathers, :wind, :string
  end
end
