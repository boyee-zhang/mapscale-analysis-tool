import React from 'react';
import { Analytics } from '@vercel/analytics/react';
import MapContainer from './components/MapContainer';
import './App.css'

const App = () => {
  return (
    <div className="App">
      <MapContainer />
      <Analytics />
    </div>
  );
};

export default App;