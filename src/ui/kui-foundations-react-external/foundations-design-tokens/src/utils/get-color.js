import designTokens from "../generated/design-tokens.js";


export function getColor(color, options = {}) {
	
	if (designTokens["primitives-color"][color]) {
		return designTokens["primitives-color"][color];
	}
	const { theme = "light", resolveToRawValue = true } = options;

	const colorFromTheme =
		designTokens[theme === "light" ? "theme-light" : "theme-dark"][color];
	if (!colorFromTheme) {
		return undefined;
	}
	
	return resolveToRawValue
		? getColor(colorFromTheme, { theme, resolveToRawValue })
		: colorFromTheme;
}
