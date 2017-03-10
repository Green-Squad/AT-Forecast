Rails.application.routes.draw do
  root 'home#index'
  resources :weathers
  resources :shelters
  resources :states
  get 'shelters/:id/hourly', to: 'shelters#hourly', as: :hourly, constraints: { format: 'json' }
  # For details on the DSL available within this file, see http://guides.rubyonrails.org/routing.html
end
