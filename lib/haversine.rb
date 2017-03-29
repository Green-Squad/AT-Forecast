# https://rosettacode.org/wiki/Haversine_formula#Ruby
class Haversine
  include Math
  Radius = 6371  # rough radius of the Earth, in kilometers

  def self.spherical_distance(start_coords, end_coords)
    lat1, long1 = deg2rad *start_coords
    lat2, long2 = deg2rad *end_coords
    2 * Radius * Math.asin(Math.sqrt(Math.sin((lat2-lat1)/2)**2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin((long2 - long1)/2)**2))
  end

  def self.deg2rad(lat, long)
    [lat * PI / 180, long * PI / 180]
  end
end
