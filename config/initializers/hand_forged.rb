if ENV['HAND_FORGED_DEMO'] == 'true'
  require Rails.root.join('hand-forged', 'weather_fixture')
  Rails.application.config.action_mailer.delivery_method = :file
  Rails.application.config.action_mailer.file_settings = { location: Rails.root.join('tmp', 'demo-mail') }
  Rails.application.config.action_mailer.default_url_options = { host: ENV.fetch('HAND_FORGED_HOST', 'handforged.atforecast.app'), protocol: 'https' }
  Rails.application.config.session_store :cookie_store, key: '_atforecast_hand_forged_session', secure: ENV['HAND_FORGED_LOCAL_TEST'] != 'true', httponly: true
  ActiveAdmin.application.site_title = 'AT Forecast — Hand-Forged Edition'
end
