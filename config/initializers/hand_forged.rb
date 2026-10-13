# Deployment isolation only; original views and application logic are unchanged.
if ENV['HAND_FORGED_DEMO'] == 'true'
  Rails.application.config.action_mailer.perform_deliveries = false
  ActionMailer::Base.perform_deliveries = false
end
