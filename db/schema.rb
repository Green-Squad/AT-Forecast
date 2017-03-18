# This file is auto-generated from the current state of the database. Instead
# of editing this file, please use the migrations feature of Active Record to
# incrementally modify your database, and then regenerate this schema definition.
#
# Note that this schema.rb definition is the authoritative source for your
# database schema. If you need to create the application database on another
# system, you should be using db:schema:load, not running all the migrations
# from scratch. The latter is a flawed and unsustainable approach (the more migrations
# you'll amass, the slower it'll run and the greater likelihood for issues).
#
# It's strongly recommended that you check this file into your version control system.

ActiveRecord::Schema.define(version: 20170310193844) do

  # These are extensions that must be enabled in order to support this database
  enable_extension "plpgsql"

  create_table "hourly_weathers", force: :cascade do |t|
    t.datetime "date"
    t.integer  "temp"
    t.string   "description"
    t.integer  "precip_chance"
    t.integer  "shelter_id"
    t.datetime "created_at",    null: false
    t.datetime "updated_at",    null: false
    t.index ["shelter_id"], name: "index_hourly_weathers_on_shelter_id", using: :btree
  end

  create_table "shelters", force: :cascade do |t|
    t.string   "name"
    t.decimal  "mileage"
    t.integer  "elevation"
    t.decimal  "long"
    t.decimal  "latt"
    t.integer  "state_id"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["state_id"], name: "index_shelters_on_state_id", using: :btree
  end

  create_table "states", force: :cascade do |t|
    t.string   "name"
    t.string   "abbreviation"
    t.datetime "created_at",   null: false
    t.datetime "updated_at",   null: false
  end

  create_table "weathers", force: :cascade do |t|
    t.datetime "weather_date"
    t.integer  "high"
    t.integer  "low"
    t.string   "description"
    t.integer  "precip_chance"
    t.integer  "shelter_id"
    t.datetime "created_at",    null: false
    t.datetime "updated_at",    null: false
    t.index ["shelter_id"], name: "index_weathers_on_shelter_id", using: :btree
  end

  add_foreign_key "hourly_weathers", "shelters"
  add_foreign_key "shelters", "states"
  add_foreign_key "weathers", "shelters"
end
