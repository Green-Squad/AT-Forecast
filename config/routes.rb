Rails.application.routes.draw do
  devise_for :admin_users, ActiveAdmin::Devise.config
  ActiveAdmin.routes(self)
  root 'home#index'
  resources :shelters, only: [:show]
  get 'nearest_shelter', to: 'home#nearest_shelter',
    as: :nearest_shelter

  # For details on the DSL available within this file, see http://guides.rubyonrails.org/routing.html
end
