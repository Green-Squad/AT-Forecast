class CreateHourlyWeathers < ActiveRecord::Migration[5.0]
  def change
    create_table :hourly_weathers do |t|
      t.datetime :date
      t.integer :temp
      t.string :description
      t.integer :precip_chance
      t.references :shelter, foreign_key: true

      t.timestamps
    end
  end
end
