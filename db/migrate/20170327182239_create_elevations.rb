class CreateElevations < ActiveRecord::Migration[5.0]
  def change
    create_table :elevations do |t|
      t.decimal :long
      t.decimal :latt
      t.integer :elevation

      t.timestamps
    end
  end
end
