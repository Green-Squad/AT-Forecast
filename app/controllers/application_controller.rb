class ApplicationController < ActionController::Base
  protect_from_forgery with: :exception

  def api_auth(token)
    key = Key.find_by_key(token)
    if (key.present?)
      true
    else
      false
    end
  end
end
