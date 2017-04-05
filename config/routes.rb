Rails.application.routes.draw do
  devise_for :admin_users, ActiveAdmin::Devise.config
  ActiveAdmin.routes(self)
  root 'home#index'
  resources :shelters, only: [:show]
  get 'nearest_shelter',  to: 'home#nearest_shelter',   as: :nearest_shelter
  get 'about',            to: 'home#about',             as: :about
end
