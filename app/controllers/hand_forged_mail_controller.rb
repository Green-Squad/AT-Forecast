class HandForgedMailController < ApplicationController
  def show
    raise ActionController::RoutingError, 'Not Found' unless ENV['HAND_FORGED_DEMO'] == 'true'
    key = Digest::SHA256.hexdigest(session.id.to_s)
    @messages = Dir[Rails.root.join('tmp', 'demo-mail', key, '*.html')].sort.map { |file| File.read(file) }
  end
end
