Rails.application.routes.draw do
  root 'home#index'
  resources :weathers
  resources :shelters
  resources :states
  get 'shelters/:id/hourly/:date', to: 'shelters#hourly', as: :hourly,
    constraints: { format: 'json' }
  get 'nearest_shelter/:mileage', to: 'home#nearest_shelter',
    as: :nearest_shelter, constraints: { mileage: /-?\d+(\.\d+)?/ }

  # For details on the DSL available within this file, see http://guides.rubyonrails.org/routing.html
end
