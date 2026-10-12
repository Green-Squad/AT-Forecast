FROM ruby:2.7.8-slim-bullseye@sha256:39f68389fd3fe8c04ed896b10cc90ba4553a42e555ae18b37b41ea8c3ce965fd AS build
WORKDIR /usr/src/app
RUN printf "deb https://archive.debian.org/debian bullseye main\\n deb https://archive.debian.org/debian-security bullseye-security main\\n" > /etc/apt/sources.list && apt-get -o Acquire::Check-Valid-Until=false update && apt-get install -y --no-install-recommends build-essential pkg-config libpq-dev libxml2-dev libxslt1-dev nodejs git && rm -rf /var/lib/apt/lists/*
COPY Gemfile Gemfile.lock ./
RUN gem install bundler -v 1.14.6 --no-document && bundle config build.nokogiri --use-system-libraries && bundle install --without development test && gem install dotenv -v 2.5.0 --no-document
ENV RUBYLIB=/usr/local/bundle/gems/dotenv-2.5.0/lib
COPY . .
RUN HAND_FORGED_DEMO=true RAILS_ENV=production SECRET_KEY_BASE=build-only-hand-forged-key DEVISE_SECRET_KEY=build-only-devise-key bundle exec rake assets:precompile
FROM ruby:2.7.8-slim-bullseye@sha256:39f68389fd3fe8c04ed896b10cc90ba4553a42e555ae18b37b41ea8c3ce965fd
RUN printf "deb https://archive.debian.org/debian bullseye main\\n deb https://archive.debian.org/debian-security bullseye-security main\\n" > /etc/apt/sources.list && apt-get -o Acquire::Check-Valid-Until=false update && apt-get install -y --no-install-recommends libpq5 libxml2 libxslt1.1 curl openssl nodejs && rm -rf /var/lib/apt/lists/*
WORKDIR /usr/src/app
COPY --from=build /usr/local/bundle /usr/local/bundle
COPY --from=build /usr/src/app /usr/src/app
RUN mkdir -p public/cached_pages/shelters tmp/pids log && useradd --uid 10001 --create-home demo && chown -R demo:demo tmp log public/cached_pages
ENV RUBYLIB=/usr/local/bundle/gems/dotenv-2.5.0/lib RAILS_ENV=production RAILS_SERVE_STATIC_FILES=true RAILS_LOG_TO_STDOUT=true TZ=America/New_York HAND_FORGED_DEMO=true PORT=8080
USER demo
EXPOSE 8080
ENTRYPOINT ["sh","hand-forged/start.sh"]
