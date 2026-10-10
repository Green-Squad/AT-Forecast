-- ISOLATED EMPTY TEST DATABASE ONLY. Schema mirrors the reader tables in db/schema.rb.
CREATE TABLE states (id serial PRIMARY KEY, name varchar, abbreviation varchar, created_at timestamp NOT NULL, updated_at timestamp NOT NULL);
CREATE TABLE shelters (id serial PRIMARY KEY, name varchar, mileage numeric, elevation integer, long numeric, latt numeric, state_id integer REFERENCES states(id), created_at timestamp NOT NULL, updated_at timestamp NOT NULL);
CREATE INDEX index_shelters_on_state_id ON shelters(state_id);
CREATE TABLE weathers (id serial PRIMARY KEY, weather_date timestamp, high integer, low integer, description varchar, shelter_id integer REFERENCES shelters(id), created_at timestamp NOT NULL, updated_at timestamp NOT NULL, wind varchar);
CREATE INDEX index_weathers_on_shelter_id ON weathers(shelter_id);
CREATE TABLE hourly_weathers (id serial PRIMARY KEY, date timestamp, temp integer, description varchar, shelter_id integer REFERENCES shelters(id), created_at timestamp NOT NULL, updated_at timestamp NOT NULL, wind varchar);
CREATE INDEX index_hourly_weathers_on_shelter_id ON hourly_weathers(shelter_id);
CREATE TABLE keys (id serial PRIMARY KEY, key varchar, created_at timestamp NOT NULL, updated_at timestamp NOT NULL);
INSERT INTO states VALUES (1,'North Carolina','NC','2020-01-01','2020-01-01'),(2,'Virginia','VA','2020-01-01','2020-01-01'),(3,'No Weather State','NW','2020-01-01','2020-01-01');
INSERT INTO shelters VALUES
 (1,'Start Shelter',100,NULL,NULL,NULL,1,'2020-01-01','2020-01-01'),
 (2,'Middle & <Shelter>',110.5,2000,-81.1,35.1,1,'2020-01-01','2020-01-01'),
 (3,'End Shelter',120,3000,-81.2,35.2,1,'2020-01-01','2020-01-01'),
 (4,'Virginia Shelter',200,4000,-80,37,2,'2020-01-01','2020-01-01'),
 (5,'Empty Forecast',210,4100,-80.1,37.1,2,'2020-01-01','2020-01-01'),
 (6,'Another Empty Forecast',220,4200,-80.2,37.2,3,'2020-01-01','2020-01-01');
INSERT INTO weathers VALUES
 (1,'2020-01-01 17:00:00',10,-1,'clear sky',2,'2020-01-01 12:00:00','2020-01-01','3 mph N'),
 (2,'2020-01-02 17:00:00',20,-2,'rain and snow',2,'2020-01-01 12:00:00','2020-01-01','4 mph S'),
 (3,'2100-01-01 17:00:00',999,999,'clear sky',2,'2020-01-01','2020-01-01','0 mph N'),
 (4,'2020-01-01 17:00:00',50,40,'scattered clouds',3,'2020-01-01','2020-01-01','5 mph W'),
 (5,'2020-01-01 17:00:00',-3,-4,'fog',4,'2020-01-01','2020-01-01','5 mph W'),
 -- Negative fractional averages must round down like Ruby integer division.
 (6,'2020-01-03 17:00:00',-2,-3,'fog',4,'2020-01-01','2020-01-01','5 mph W');
INSERT INTO hourly_weathers VALUES
 (1,'2020-01-01 14:00:00',NULL,NULL,2,'2020-01-01','2020-01-01',NULL),
 (2,'2020-01-01 17:00:00',10,'clear sky',2,'2020-01-01','2020-01-01','3 mph N'),
 -- Eastern day boundary: 04:59 UTC still belongs to January 1; 05:00 belongs to January 2.
 (3,'2020-01-02 04:59:00',1,'mist',2,'2020-01-01','2020-01-01','1 mph N'),
 (4,'2020-01-01 17:00:00',40,'clouds',3,'2020-01-01','2020-01-01','5 mph W');
INSERT INTO keys VALUES (1,'fixture-key','2020-01-01','2020-01-01');
CREATE ROLE forecast_reader LOGIN PASSWORD 'fixture-only';
GRANT CONNECT ON DATABASE at_forecast_test TO forecast_reader;
GRANT USAGE ON SCHEMA public TO forecast_reader;
GRANT SELECT ON states,shelters,weathers,hourly_weathers,keys TO forecast_reader;
