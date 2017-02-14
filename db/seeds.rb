states = [
            ['Georgia', 'GA'],
            ['North Carolina', 'NC'],
            ['Tennessee', 'TN'],
            ['Virginia', 'VA'],
            ['Maryland', 'MD'],
            ['Pennsylvania', 'PA'],
            ['New Jersey', 'NJ'],
            ['New York', 'NY'],
            ['Connecticut', 'CT'],
            ['Massachusetts', 'MA'],
            ['Vermont', 'VT'],
            ['New Hampshire', 'NH'],
            ['Maine', 'ME']
          ]
states.each do |state|
  State.create(name: state.first, abbreviation: state.last)
end
