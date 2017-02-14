class CreateShelters < ActiveRecord::Migration[5.0]
  def change
    create_table :shelters do |t|
      t.string :name
      t.decimal :mileage
      t.integer :elevation
      t.decimal :long
      t.decimal :latt
      t.references :state, foreign_key: true

      t.timestamps
    end
  end
end
