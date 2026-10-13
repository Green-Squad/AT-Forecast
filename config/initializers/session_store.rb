# Be sure to restart your server when you modify this file.

if ENV['HAND_FORGED_DEMO'] == 'true'
  Rails.application.config.session_store :cookie_store, key: '_atforecast_hand_forged_session', secure: ENV['HAND_FORGED_LOCAL_TEST'] != 'true', httponly: true
else
  Rails.application.config.session_store :cookie_store, key: '_Fast-AT-Weather_session'
end
