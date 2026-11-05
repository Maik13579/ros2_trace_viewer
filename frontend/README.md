# Frontend

Native JavaScript ES modules and Canvas, with no runtime or build dependency on
npm, Node.js, a CDN, or a frontend development server. Source is in `src/`;
`python3 frontend/build.py` copies these assets into the Python package's
`static/` directory. Prebuilt assets are included in the package.

`shared.js` owns applied filters and a separate settings draft. Only **Apply
filters** commits the draft. Selection never changes filters or view/zoom;
**Show on timeline** is an explicit navigation action. `timeline.js` uses the
selected time interval as the extent for its axis, scrollbar, and overview.

The browser only consumes normalized `/api/` responses. CTF, ROS initialization
semantics, matching, and statistics remain on the backend.
