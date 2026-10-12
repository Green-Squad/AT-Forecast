require 'fileutils'
require 'digest'
require 'securerandom'
module HandForgedMailSession
  extend ActiveSupport::Concern
  included do
    around_action :capture_hand_forged_mail
  end
  def capture_hand_forged_mail
    previous = Thread.current[:hand_forged_mail_session]
    Thread.current[:hand_forged_mail_session] = session.id.to_s
    yield
  ensure
    Thread.current[:hand_forged_mail_session] = previous
  end
end
class HandForgedMailDelivery
  attr_accessor :settings
  def initialize(settings = {})
    @settings = settings
  end
  def deliver!(mail)
    key = Digest::SHA256.hexdigest(Thread.current[:hand_forged_mail_session].to_s)
    folder = Rails.root.join('tmp', 'demo-mail', key)
    FileUtils.mkdir_p(folder)
    body = mail.html_part ? mail.html_part.body.decoded : mail.body.decoded
    File.write(folder.join(SecureRandom.hex(16) + '.html'), body)
  end
end
