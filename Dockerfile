# Use an official Ruby image as a parent image
FROM ruby:2.7

# Set the working directory in the container
WORKDIR /usr/src/app

# Copy the Gemfile and Gemfile.lock into the container
COPY Gemfile Gemfile.lock ./

# Install any needed gems specified in Gemfile
RUN bundle install

# Copy the rest of your app's source code from your host to your container
COPY . .

# Add metadata to the image to describe which port the container is listening on at runtime
EXPOSE 3000

# Start the main process (in this case, Puma server)
CMD ["rails", "server", "-b", "0.0.0.0"]
