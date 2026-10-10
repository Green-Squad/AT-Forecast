# LOCAL/ISOLATED COMPARISON ONLY. Run with: bundle exec rails runner tools/rails_compare_server.rb
# Original controllers, models, ERB, and Jbuilder remain unchanged.
# This wrapper adds equivalent measurements and an explicit homepage cache-off mode.
require 'webrick'
require 'rack/handler/webrick'
require 'fileutils'
raise 'Set AT_FORECAST_TEST_DATABASE=isolated' unless ENV['AT_FORECAST_TEST_DATABASE'] == 'isolated'
Rails.application.config.filter_parameters += [:api_key, :coords]
Rails.logger.level = Logger::FATAL

class ComparisonMetrics
  def initialize(app); @app = app; end
  def call(env)
    stats = { count: 0, ms: 0.0, hits: 0 }
    subscriber = ActiveSupport::Notifications.subscribe('sql.active_record') do |*args|
      event = ActiveSupport::Notifications::Event.new(*args)
      next unless Thread.current[:comparison_stats].equal?(stats)
      next if ['SCHEMA', 'TRANSACTION'].include?(event.payload[:name])
      # Rails 5.0 identifies cache events by name; newer versions also set :cached.
      if event.payload[:cached] || event.payload[:name] == 'CACHE'
        stats[:hits] += 1
      else
        stats[:count] += 1; stats[:ms] += event.duration
      end
    end
    Thread.current[:comparison_stats] = stats
    status, headers, body = @app.call(env)
    headers['X-DB-Query-Count'] = stats[:count].to_s
    headers['X-DB-Query-Cache-Hits'] = stats[:hits].to_s
    headers['Server-Timing'] = "db;dur=#{stats[:ms].round(3)}"
    [status, headers, body]
  ensure
    Thread.current[:comparison_stats] = nil
    ActiveSupport::Notifications.unsubscribe(subscriber) if subscriber
  end
end
if ENV['CACHE_MODE'] == 'off'
  HomeController.class_eval do
    alias_method :original_index, :index
    def index
      return original_index unless request.format.html?
      @states = Weatherable.get_states
      # Preserve HtmlCompressor work from the uncached original; only skip the cache-file read/write.
      render html: HtmlCompressor::Compressor.new.compress(render_to_string(template: 'home/index')).html_safe
    end
  end
  SheltersController.perform_caching = false
end
FileUtils.mkdir_p(Rails.root.join('public','cached_pages','shelters'))
Rack::Handler::WEBrick.run(ComparisonMetrics.new(Rails.application),
  Host: '0.0.0.0', Port: ENV.fetch('PORT','8082').to_i,
  AccessLog: [], Logger: WEBrick::Log.new(File::NULL))
