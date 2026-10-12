Rails.application.routes.draw do
  devise_for :admin_users, ActiveAdmin::Devise.config
  ActiveAdmin.routes(self)
  get 'hand-forged/mail', to: 'hand_forged_mail#show', as: :hand_forged_mail if ENV['HAND_FORGED_DEMO'] == 'true'
  root 'home#index'
  resources :shelters, only: [:show]
  get 'nearest_shelter',  to: 'home#nearest_shelter',   as: :nearest_shelter
  get 'about',            to: 'home#about',             as: :about
  get 'index.json',       to: 'home#index',             as: :index_json,
    constraints: lambda { |req| req.format == :json }
end
