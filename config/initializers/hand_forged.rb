if ENV['HAND_FORGED_DEMO'] == 'true'
  require Rails.root.join('hand-forged', 'weather_fixture')
  require Rails.root.join('hand-forged', 'mail_capture')
  ActionMailer::Base.add_delivery_method :hand_forged, HandForgedMailDelivery
  Rails.application.config.action_mailer.delivery_method = :hand_forged
  ActionMailer::Base.delivery_method = :hand_forged
  Rails.application.config.active_job.queue_adapter = :inline
  Rails.application.config.to_prepare do
    ApplicationController.include(HandForgedMailSession) unless ApplicationController < HandForgedMailSession
  end
  Rails.application.config.action_mailer.default_url_options = ENV['HAND_FORGED_LOCAL_TEST'] == 'true' ? { host: '127.0.0.1', port: 8080, protocol: 'http' } : { host: ENV.fetch('HAND_FORGED_HOST', 'handforged.atforecast.app'), protocol: 'https' }
  ActionMailer::Base.default_url_options = Rails.application.config.action_mailer.default_url_options
  ActiveAdmin.application.site_title = 'AT Forecast — Hand-Forged Edition'
end
