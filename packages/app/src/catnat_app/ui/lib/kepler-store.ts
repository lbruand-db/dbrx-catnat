/**
 * Redux store hosting the kepler.gl reducer.
 *
 * kepler.gl is built on Redux + redux-thunk for the analytical pane's state
 * (layers, filters, time-cursor). We mount its reducer under the `keplerGl`
 * key — Kepler's components look for state at `state.keplerGl` by default.
 *
 * The store is *only* used by the Kepler pane. The rest of the app uses
 * TanStack Query for server state; there's no global Redux footprint outside
 * this module.
 */
import { keplerGlReducer } from "@kepler.gl/reducers";
import { applyMiddleware, combineReducers, compose, createStore } from "redux";
// redux-thunk ships as a peer dep of kepler.gl/reducers — we import the
// `thunk` middleware via the package's default export.
// biome-ignore lint/suspicious/noExplicitAny: kepler-thunk typing is broken across versions
import thunk from "redux-thunk";

const rootReducer = combineReducers({
    keplerGl: keplerGlReducer,
});

// `compose` lets us wire the Redux devtools extension when present without
// importing the dev-only `@redux-devtools/extension` package as a runtime
// dependency.
// biome-ignore lint/suspicious/noExplicitAny: window devtools shape isn't typed
const composeEnhancers: typeof compose =
    (typeof window !== "undefined" &&
        // biome-ignore lint/suspicious/noExplicitAny: devtools global has no shipped type
        (window as any).__REDUX_DEVTOOLS_EXTENSION_COMPOSE__) ||
    compose;

export const keplerStore = createStore(rootReducer, composeEnhancers(applyMiddleware(thunk)));

export type KeplerRootState = ReturnType<typeof keplerStore.getState>;
