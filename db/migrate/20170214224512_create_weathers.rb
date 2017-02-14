class CreateWeathers < ActiveRecord::Migration[5.0]
  def change
    create_table :weathers do |t|
      t.datetime :date
      t.integer :high
      t.integer :low
      t.string :description
      t.integer :precip_chance
      t.references :shelter, foreign_key: true

      t.timestamps
    end
  end
end
