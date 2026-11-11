/* Copyright 2026 Jelly-RayTian. This file was taken from https://github.com/willfaust/Madeira (pull request #400)
 * under GPL-3.0-or-later with the Madeira Converter Exception. See THIRD-PARTY-NOTICES.md. */
/* Vulkan's surface extent is in Wine client pixels, while the compositor
 * places windows in UIKit points. Keep the layer's local coordinates in
 * client pixels and scale its placement, rather than its bounds. MoltenVK
 * compares drawableSize with bounds * contentsScale when acquiring images.
 * Main thread only; applies to standalone compositor sublayers, not UIView
 * backing layers. SPDX-License-Identifier: GPL-3.0-or-later */
#pragma once
#import <QuartzCore/CAMetalLayer.h>
#include <math.h>

static inline BOOL madeira_vulkan_layer_geometry(CAMetalLayer *layer,
                                                 CGRect pointFrame,
                                                 CGSize clientPixels)
{
    if (!layer || !isfinite(clientPixels.width) || !isfinite(clientPixels.height) ||
        clientPixels.width <= 0 || clientPixels.height <= 0 ||
        !isfinite(pointFrame.origin.x) || !isfinite(pointFrame.origin.y) ||
        !isfinite(pointFrame.size.width) || !isfinite(pointFrame.size.height) ||
        pointFrame.size.width <= 0 || pointFrame.size.height <= 0)
        return NO;
    layer.anchorPoint = CGPointZero;
    layer.contentsScale = 1;
    layer.bounds = (CGRect){CGPointZero, clientPixels};
    layer.affineTransform = CGAffineTransformMakeScale(
        pointFrame.size.width / clientPixels.width,
        pointFrame.size.height / clientPixels.height);
    layer.position = pointFrame.origin;
    return YES;
}
