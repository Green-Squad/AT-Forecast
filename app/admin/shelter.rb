ActiveAdmin.register Shelter do
  if ENV['HAND_FORGED_DEMO'] == 'true'
    permit_params :name, :mileage, :elevation, :long, :latt, :state_id
    member_action :refresh_demo_weather, method: :post do
      resource.update_weather
      resource.update_hourly_weather
      FileUtils.rm_rf(Rails.root.join('public', 'cached_pages'))
      FileUtils.mkdir_p(Rails.root.join('public', 'cached_pages', 'shelters'))
      redirect_to resource_path, notice: 'Sample forecasts refreshed with the original weather transformation.'
    end
    action_item :refresh_demo_weather, only: :show do
      link_to 'Refresh sample forecasts', refresh_demo_weather_admin_shelter_path(resource), method: :post
    end
  end


# See permitted parameters documentation:
# https://github.com/activeadmin/activeadmin/blob/master/docs/2-resource-customization.md#setting-up-strong-parameters
#
# permit_params :list, :of, :attributes, :on, :model
#
# or
#
# permit_params do
#   permitted = [:permitted, :attributes]
#   permitted << :other if params[:action] == 'create' && current_user.admin?
#   permitted
# end


end
